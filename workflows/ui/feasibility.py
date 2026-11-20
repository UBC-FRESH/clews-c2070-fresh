"""Documented national harvest feasibility screen for the C2070 UI and runner.

The energy model receives harvest as an exogenous input and does not price the
log, the landbase, or the provincial allocation.  This module therefore carries
the narrow, documented screen from ``planning/harvest-aac-feasibility.md``:
100/119 Mm3 pass with tenure caveats, 150 Mm3 is conditional because no
provincial allocation model exists, and 200 Mm3 fails the documented AAC/NFD
screen.  It is a scenario-admissibility guard, not a forest-sector model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "DOCUMENTED_AAC_PARTIAL_SUM_M3",
    "MAX_NORMAL_HARVEST_M3",
    "NFD_WOOD_SUPPLY_SUM_M3",
    "OBSERVED_HARVEST_M3",
    "HarvestFeasibility",
    "assess_harvest_feasibility",
]

OBSERVED_HARVEST_M3 = 118_977_339
MAX_NORMAL_HARVEST_M3 = 150_000_000
DOCUMENTED_AAC_PARTIAL_SUM_M3 = 157_343_387
NFD_WOOD_SUPPLY_SUM_M3 = 207_680_142

FeasibilityStatus = Literal["pass", "conditional", "fail"]


@dataclass(frozen=True, slots=True)
class HarvestFeasibility:
    """Trusted feasibility result for one resolved national harvest path."""

    status: FeasibilityStatus
    notes: tuple[str, ...]
    pareto_eligible: bool
    diagnostic: bool

    @property
    def label(self) -> str:
        return {
            "pass": "pass — within documented national screens",
            "conditional": "conditional — national bound only",
            "fail": "fail — above documented AAC/NFD",
        }[self.status]

    def summary(self) -> dict[str, object]:
        return {
            "status": self.status,
            "label": self.label,
            "notes": list(self.notes),
            "pareto_eligible": self.pareto_eligible,
            "diagnostic": self.diagnostic,
            "documented_aac_partial_sum_m3": DOCUMENTED_AAC_PARTIAL_SUM_M3,
            "nfd_wood_supply_sum_m3": NFD_WOOD_SUPPLY_SUM_M3,
            "max_normal_harvest_m3": MAX_NORMAL_HARVEST_M3,
            "observed_harvest_m3": OBSERVED_HARVEST_M3,
        }


_TENURE_CAVEATS = (
    (
        "NB, NS, and QC report Crown AAC while FSD harvest is total provincial harvest; "
        "a total-harvest/Crown-AAC comparison is not automatically an AAC breach."
    ),
    (
        "The 157.34 Mm3 AAC sum is partial and mixed-basis (MB, PE, and NU are missing); "
        "it is not a legal national AAC."
    ),
)


def assess_harvest_feasibility(
    harvest_m3_per_year: float,
    *,
    pareto_eligible: bool = False,
) -> HarvestFeasibility:
    """Classify one resolved national harvest level against the documented screen.

    ``pareto_eligible`` is only honoured for a passing level.  The caller still
    decides whether the specific scenario is the canonical energy case; a live
    or sensitivity run must pass ``pareto_eligible=False``.
    """

    if isinstance(harvest_m3_per_year, bool) or not isinstance(
        harvest_m3_per_year, (int, float)
    ):
        raise TypeError("national harvest must be a finite number")
    level = float(harvest_m3_per_year)
    if not math.isfinite(level) or level <= 0.0:
        raise ValueError("national harvest must be a finite positive value")

    if level > MAX_NORMAL_HARVEST_M3:
        notes = (
            (
                f"{level / 1e6:.3f} Mm3/yr is above the documented 150,000,000 m3/yr "
                "normal-run bound."
            ),
            (
                "200 Mm3 is 42.66 Mm3 above the partial AAC sum and exceeds provincial "
                "NFD in BC, AB, NB, NL, and PE; lower levels are also above the bound."
            ),
            (
                "This case is a historical/diagnostic stress test only. It is not "
                "admissible under the documented AAC/NFD screen and is not Pareto eligible."
            ),
            *_TENURE_CAVEATS,
        )
        return HarvestFeasibility(
            status="fail",
            notes=notes,
            pareto_eligible=False,
            diagnostic=True,
        )

    if level > OBSERVED_HARVEST_M3:
        notes = (
            (
                "150 Mm3 is the adopted high scenario bound and is 7.34 Mm3 below the "
                "partial AAC sum, but the status-quo provincial mix exceeds the BC repo "
                "AAC (3.79 Mm3; 5.64 against the 2028+ hold), NB Crown AAC (5.54 Mm3), "
                "NB NFD (2.19 Mm3), NS Crown AAC (1.21 Mm3; under the combined "
                "Crown+private ceiling), and the NT proxy (0.01 Mm3)."
            ),
            (
                "A screening provincial LP allocation exists for residue-factor "
                "weighting, but the runner does not enforce provincial AAC/NFD caps. "
                "The national bound is conditional, not a feasible provincial-mix case."
            ),
            *_TENURE_CAVEATS,
            (
                "Harvest is exogenous and free in the energy objective; this is not a "
                "policy or Pareto frontier."
            ),
        )
        return HarvestFeasibility(
            status="conditional",
            notes=notes,
            pareto_eligible=False,
            diagnostic=False,
        )

    notes = (
        (
            "Below the documented partial AAC sum (157.34 Mm3) and the NFD potential "
            "supply sum (207.68 Mm3); no national AAC or NFD exceedance."
        ),
        (
            "Tenure caveats apply: NB and NS exceed Crown-only AAC but are within or "
            "near documented combined/NFD ceilings; 119 Mm3 is the observed 2024 base, "
            "not a sustainable-supply estimate."
        ),
        *_TENURE_CAVEATS,
        (
            "Harvest is exogenous and free in the energy objective; this is a "
            "conditional energy result, not a policy or forecast."
        ),
    )
    return HarvestFeasibility(
        status="pass",
        notes=notes,
        pareto_eligible=bool(pareto_eligible),
        diagnostic=False,
    )
