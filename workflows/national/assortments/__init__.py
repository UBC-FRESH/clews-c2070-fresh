"""National stand-to-tree disaggregation and assortment contracts.

The package turns a stand-level target (volume, species composition, age) into
a volume-constrained synthetic tree list, bucks it with the existing BC
interior bucking layer, and aggregates logs and residue into an assortment
matrix.  It is a prototype contract, not an operational national model.
"""

from __future__ import annotations

from .assortments import (
    AssortmentError,
    build_assortment_matrix,
    conservation_summary,
)
from .distributions import (
    DistributionFit,
    DistributionFitError,
    FitCandidate,
    fit_dbh_distributions,
    fit_distribution_for_target,
)
from .facility import (
    FacilityAssignment,
    FacilityContract,
    FacilityError,
    FeedstockOffer,
    assign_feedstock,
)
from .height import (
    HeightModelBundle,
    HeightModelError,
    HeightModelRecord,
    fit_height_models,
)
from .synthesize import (
    DistributionSpec,
    SynthesisError,
    SynthesisTarget,
    SynthesizedStand,
    normalize_species_weights,
    synthesize_tree_list,
)

__all__ = [
    "AssortmentError",
    "DistributionFit",
    "DistributionFitError",
    "DistributionSpec",
    "FacilityAssignment",
    "FacilityContract",
    "FacilityError",
    "FeedstockOffer",
    "FitCandidate",
    "HeightModelBundle",
    "HeightModelError",
    "HeightModelRecord",
    "SynthesisError",
    "SynthesisTarget",
    "SynthesizedStand",
    "assign_feedstock",
    "build_assortment_matrix",
    "conservation_summary",
    "fit_dbh_distributions",
    "fit_distribution_for_target",
    "fit_height_models",
    "normalize_species_weights",
    "synthesize_tree_list",
]
